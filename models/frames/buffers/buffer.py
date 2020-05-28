from typing import Union, Dict, List, Tuple, Callable
from .transition import Transition, Scalar
import torch as t
import random


class Buffer:
    def __init__(self, buffer_size, buffer_device="cpu", *_, **__):
        """
        Create a buffer instance.

        Buffer stores a series of transition objects and functions
        as a ring buffer.

        .. seealso:: :class:`.transition.TransitionBase`
        .. seealso:: :class:`.transition.Transition`

        During sampling, the tensors in "state", "action" and "next_state"
        dictionaries, along with "reward", will be concatenated in dimension 0.
        any other custom keys specified in **kwargs will not be concatenated.

        Args:
            buffer_size: Maximum buffer size.
            buffer_device: Device where buffer is stored.
        """
        self.buffer_size = buffer_size
        self.buffer_device = buffer_device
        self.buffer = []
        self.index = 0

    def append(self, transition: Union[Transition, Dict],
               required_attrs=("state", "action", "next_state",
                               "reward", "terminal")):
        """
        Store a transition object to buffer.

        Args:
            transition: A transition object.
            required_attrs: Required attributes.

        Raises:
            ``ValueError`` if transition object doesn't have required
            attributes in ``required_attrs`` or has different attributes
            compared to other transition objects stored in buffer.
        """
        if isinstance(transition, dict):
            transition = Transition(**transition)
        if not transition.has_keys(required_attrs):
            missing_keys = set(required_attrs) - set(transition.keys())
            raise ValueError("Transition object missing attributes: {}"
                             .format(missing_keys))
        transition.to(self.buffer_device)

        if self.size() != 0 and self.buffer[0].keys() != transition.keys():
            raise ValueError("Transition object has different attributes!")

        if self.size() > self.buffer_size:
            # trim buffer to buffer_size
            self.buffer = self.buffer[(self.size() - self.buffer_size):]
        if self.size() == self.buffer_size:
            # ring buffer storage
            position = self.index
            self.buffer[self.index] = transition
            self.index += 1
            self.index %= self.buffer_size
        else:
            # append if not full
            self.buffer.append(transition)
            position = len(self.buffer) - 1
        return position

    def size(self):
        """
        Returns:
            Length of current buffer.
        """
        return len(self.buffer)

    def clear(self):
        self.buffer.clear()

    @staticmethod
    def sample_method_random_unique(buffer: List[Transition], batch_size: int) \
            -> Tuple[List[Transition], int]:
        """
        Sample unique random samples from buffer.

        Notes:
            Sampled size could be any value from 0 to batch_size.
        """
        if len(buffer) < batch_size:
            batch = random.sample(buffer, len(buffer))
            real_num = len(buffer)
        else:
            batch = random.sample(buffer, batch_size)
            real_num = batch_size
        return batch, real_num

    @staticmethod
    def sample_method_random(buffer: List[Transition], batch_size: int) \
            -> Tuple[List[Transition], int]:
        """
        Sample random samples from buffer.

        Notes:
            Sampled size could be any value from 0 to batch_size.
        """
        indexes = [random.randint(0, len(buffer) - 1)
                   for _ in range(batch_size)]
        batch = [buffer[i] for i in indexes]
        return batch, batch_size

    @staticmethod
    def sample_method_all(buffer: List[Transition], _) \
            -> Tuple[List[Transition], int]:
        """
        Sample all samples from buffer. Always return the whole buffer.
        """
        return buffer, len(buffer)

    def sample_batch(self,
                     batch_size: int,
                     concatenate: bool = True,
                     device: Union[str, t.device] = None,
                     sample_method: Union[Callable, str] = "random_unique",
                     sample_attrs: List[str] = None,
                     additional_concat_attrs: List[str] = None,
                     *_, **__):
        """
        Sample a random batch from buffer.

        Notes:
            Default sample methods are defined as static class methods:

            .. seealso:: :meth:`sample_method_random_unique`
            .. seealso:: :meth:`sample_method_random`
            .. seealso:: :meth:`sample_method_all`

        Notes:
            "Concatenation"
            means ``torch.cat([...], dim=0)`` for tensors,
            and ``torch.tensor([...]).view(batch_size, 1)`` for scalars.

        Warnings:
            Custom attributes must not contain tensors. And only scalar custom
            attributes can be concatenated, such as ``int``, ``float``,
            ``bool``.

        Args:
            batch_size: A hint size of the result sample. actual sample size
                        depends on your sample method.
            sample_method: Sample method, could be one of:
                           ``("random", "random_unique", "all")``,
                           or a function:
                           ``func(list, batch_size)
                           -> (result list, result_size)``
            concatenate: Whether concatenate state, action and next_state
                         in dimension 0.
                         If ``True``, for each value in dictionaries of major
                         attributes. and each value of sub attributes, returns
                         a concatenated tensor. Custom Attributes specified in
                         ``additional_concat_attrs`` will also be concatenated.
                         If ``False``, return a list of tensors.
            device:      Device to copy to.
            sample_attrs: If sample_keys is specified, then only specified keys
                         of the transition object will be sampled. You may use
                         ``"*"`` as a wildcard to collect remaining keys.
            additional_concat_attrs: additional custom keys needed to be
                         concatenated,

        Returns:
            Batch size, Sampled attribute values in the same order as
            ``sample_keys``.

            Sampled attribute values is a tuple. If batch size is zero,
            then ``None`` for each sampled keys.

            For major attributes, result are dictionaries of tensors with
            the same keys in your transition objects.

            For sub attributes, result are tensors.

            For custom attributes, if they are not in
            ``additional_concat_attrs``, then lists, otherwise tensors.
        """
        if isinstance(sample_method, str):
            if not hasattr(self, "sample_method_" + sample_method):
                raise RuntimeError("Cannot find specified sample method: {}"
                                   .format(sample_method))
            sample_method = getattr(self, "sample_method_" + sample_method)
        batch, batch_size = sample_method(self.buffer, batch_size)

        if device is None:
            device = self.buffer_device
        if sample_attrs is None:
            sample_attrs = batch[0].keys()
        if additional_concat_attrs is None:
            additional_concat_attrs = []

        return \
            batch_size, \
            self.post_process_batch(batch, device, concatenate,
                                    sample_attrs, additional_concat_attrs)

    @classmethod
    def post_process_batch(cls,
                           batch: List[Transition],
                           device: Union[str, t.device],
                           concatenate: bool,
                           sample_attrs: List[str],
                           additional_concat_attrs: List[str]):
        """
        Post-process (concatenate) sampled batch.

        .. seealso:: :meth:`sample_batch`
        """
        result = []
        used_keys = []
        if len(batch) == 0:
            return [None] * len(sample_attrs)

        major_attr = set(batch[0].major_attr)
        sub_attr = set(batch[0].sub_attr)
        for attr in sample_attrs:
            if attr in major_attr:
                tmp_dict = {}
                for sub_k in batch[0][attr].keys():
                    tmp_dict[sub_k] = cls.make_tensor_from_batch(
                        [item[attr][sub_k].to(device) for item in batch],
                        device, concatenate
                    )
                result.append(tmp_dict)
                used_keys.append(attr)
            elif attr in sub_attr:
                result.append(cls.make_tensor_from_batch(
                    [item[attr] for item in batch],
                    device, concatenate
                ))
                used_keys.append(attr)
            elif attr == "*":
                # select custom keys
                for remain_k in batch[0].keys():
                    if (remain_k not in major_attr and
                            remain_k not in sub_attr and
                            remain_k not in used_keys):
                        result.append(cls.make_tensor_from_batch(
                            [item[remain_k] for item in batch],
                            device,
                            concatenate and attr in additional_concat_attrs
                        ))
            else:
                result.append(cls.make_tensor_from_batch(
                    [item[attr] for item in batch],
                    device,
                    concatenate and attr in additional_concat_attrs
                ))
                used_keys.append(attr)
        return tuple(result)

    @staticmethod
    def make_tensor_from_batch(batch: List[Scalar, t.Tensor],
                               device: Union[str, t.device],
                               concatenate: bool):
        """
        Make a tensor from a batch of data.
        Will concatenate input tensors in dimension 0,
        Or create a tensor of size (batch_size, 1) for scalars.

        .. seealso:: :meth:`sample_batch`
        Args:
            batch: Batch data.
            device: Device to move data to
            concatenate: Whether performing concatenation.

        Returns:
            ``None`` if batch is empty,
            or tensor depends on your data (if concatenate),
            or original batch (if not concatenate).
        """
        if len(batch) == 0:
            return None
        if concatenate:
            item = batch[0]
            batch_size = len(batch)
            if t.is_tensor(item):
                batch = [it.to(device) for it in batch]
                return t.cat(batch, dim=0).to(device)
            else:
                return t.tensor(batch, device=device).view(batch_size, -1)
        else:
            return batch

    def __reduce__(self):
        # for pickling
        return self.__class__, (self.buffer_size, self.buffer_device)
